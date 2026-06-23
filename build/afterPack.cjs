// electron-builder afterPack hook (macOS).
//
// We ship UNSIGNED (no Apple Developer ID): `CSC_IDENTITY_AUTO_DISCOVERY=false`
// tells electron-builder to skip signing entirely, which leaves the bundle with
// only the linker's partial ad-hoc signature and NO valid `_CodeSignature`. On
// Apple Silicon (M1–M5) macOS then refuses to launch it with the misleading
// "is damaged and can't be opened" error — even after the user removes the
// download quarantine.
//
// This hook makes the build self-contained and runnable on any Apple Silicon Mac
// (after the recipient removes the quarantine flag — see README) by:
//   1. deleting any dangling symlinks that leaked into the bundle (e.g. the dev
//      workspace links pointing at /Volumes/… on the BUILD machine, which are
//      broken everywhere else and also break codesign's strict validation), then
//   2. applying a proper, VALID ad-hoc signature to the whole bundle.
//
// Note: ad-hoc ≠ notarized. The only way to remove the Gatekeeper prompt entirely
// is signing with an Apple Developer ID + notarization.
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

function removeDanglingSymlinks(root) {
  let removed = 0;
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isSymbolicLink()) {
        // fs.existsSync follows the link → false means the target is missing.
        if (!fs.existsSync(p)) {
          try {
            fs.unlinkSync(p);
            removed++;
          } catch { /* ignore */ }
        }
      } else if (e.isDirectory()) {
        walk(p);
      }
    }
  };
  walk(root);
  return removed;
}

function removeAppleDoubleFiles(root) {
  let removed = 0;
  const walk = (dir) => {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const target = path.join(dir, entry.name);
      if (entry.name.startsWith("._")) {
        try {
          fs.rmSync(target, { force: true, recursive: entry.isDirectory() });
          removed++;
        } catch { /* ignore metadata that cannot be deleted */ }
      } else if (entry.isDirectory() && !entry.isSymbolicLink()) {
        walk(target);
      }
    }
  };
  walk(root);
  return removed;
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "darwin") return;
  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);

  const removed = removeDanglingSymlinks(appPath);
  if (removed) console.log(`[afterPack] removed ${removed} dangling symlink(s) from bundle`);
  const removedMetadata = removeAppleDoubleFiles(appPath);
  if (removedMetadata) console.log(`[afterPack] removed ${removedMetadata} AppleDouble metadata file(s) from bundle`);

  // --force replaces the partial signature; --deep covers nested frameworks and
  // helpers; "-" is the ad-hoc identity. Verified to produce a strict-valid sig.
  execFileSync("codesign", ["--force", "--deep", "--sign", "-", appPath], { stdio: "inherit" });
  console.log(`[afterPack] ad-hoc signed ${appPath}`);
};
