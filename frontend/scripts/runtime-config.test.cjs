const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { initializeRuntimeConfig } = require("../electron/runtime-config.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "coc-config-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const profile = path.join(root, "profile");
  const source = path.join(root, "release.json");
  fs.writeFileSync(source, JSON.stringify({ dryRun: true }));
  return { root, profile, source, config: path.join(profile, "bot_config.json"), marker: path.join(profile, ".config-initialized") };
}

test("first launch copies release defaults and marks initialization; lost config stays missing", (t) => {
  const f = fixture(t);
  assert.equal(initializeRuntimeConfig(f.profile, f.source), f.config);
  assert.equal(JSON.parse(fs.readFileSync(f.config)).dryRun, true);
  assert.ok(fs.existsSync(f.marker));
  fs.unlinkSync(f.config);
  initializeRuntimeConfig(f.profile, f.source);
  assert.equal(fs.existsSync(f.config), false);
});

test("legacy live or invalid config is preserved and marked", (t) => {
  const f = fixture(t);
  fs.mkdirSync(f.profile);
  for (const content of ['{"dryRun":false}', '{broken']) {
    fs.writeFileSync(f.config, content);
    initializeRuntimeConfig(f.profile, f.source);
    assert.equal(fs.readFileSync(f.config, "utf8"), content);
    assert.ok(fs.existsSync(f.marker));
    fs.unlinkSync(f.marker);
  }
});

test("failed partial copy leaves no marker or config and next launch retries", (t) => {
  const f = fixture(t);
  const copy = fs.copyFileSync;
  fs.copyFileSync = (_source, target) => {
    fs.writeFileSync(target, "partial");
    throw new Error("Simulated disk failure");
  };
  try {
    assert.throws(() => initializeRuntimeConfig(f.profile, f.source), /disk failure/);
  } finally {
    fs.copyFileSync = copy;
  }
  assert.equal(fs.existsSync(f.marker), false);
  assert.equal(fs.existsSync(f.config), false);
  assert.deepEqual(fs.readdirSync(f.profile), []);
  initializeRuntimeConfig(f.profile, f.source);
  assert.ok(fs.existsSync(f.marker));
  assert.equal(JSON.parse(fs.readFileSync(f.config)).dryRun, true);
});
