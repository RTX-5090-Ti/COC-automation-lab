const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const script = fs.readFileSync(path.join(__dirname, "bundle-backend.cjs"), "utf8");
const projectRoot = path.resolve(__dirname, "../..");
const sourcePath = path.join(projectRoot, "config", "bot_config.json");
const source = JSON.stringify({ dryRun: false, farmMode: "builder_base", battlesPerSession: 5 });

function bundle({ unsafeOutput = false } = {}) {
  const files = new Map([[sourcePath, source]]);
  vm.runInNewContext(script, {
    __dirname,
    console: { log() {} },
    process: { env: {}, exit() { throw new Error("Build failed"); } },
    require(name) {
      if (name === "node:path") return path;
      if (name === "node:fs") return {
        existsSync: () => true,
        mkdirSync() {},
        rmSync() {},
        readFileSync: (file) => {
          assert.ok(files.has(file), `Missing file: ${file}`);
          return files.get(file);
        },
        writeFileSync: (file, value) => files.set(file, value),
      };
      if (name === "node:child_process") return {
        spawnSync(_python, args) {
          const configArg = args.find((arg) => arg.endsWith(`${path.delimiter}config`));
          const input = configArg.slice(0, -`${path.delimiter}config`.length);
          assert.ok(files.has(input), "Bundle must use the generated config file");
          const bundled = path.join(projectRoot, "build/backend-runtime/desktop_backend/_internal/config/bot_config.json");
          files.set(bundled, unsafeOutput ? source : files.get(input));
          return { status: 0 };
        },
      };
      throw new Error(`Unexpected module: ${name}`);
    },
  });
  return files;
}

test("portable forces dry-run without changing developer settings or other defaults", () => {
  const files = bundle();
  assert.equal(files.get(sourcePath), source);
  const generated = JSON.parse(files.get(path.join(projectRoot, "build/portable-config/bot_config.json")));
  assert.deepEqual(generated, { dryRun: true, farmMode: "builder_base", battlesPerSession: 5 });
});

test("reject a bundle containing live defaults", () => {
  assert.throws(() => bundle({ unsafeOutput: true }), /must ship with dryRun: true/);
});
