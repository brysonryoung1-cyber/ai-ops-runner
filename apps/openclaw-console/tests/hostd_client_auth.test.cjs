const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const HOSTD_SOURCE_PATH = path.join(__dirname, "..", "src", "lib", "hostd.ts");
const HOSTD_TOKEN_FILE = "/etc/ai-ops-runner/secrets/openclaw_admin_token";
const HOSTD_ENV_FILE = "/etc/ai-ops-runner/secrets/openclaw_hostd.env";

function setEnv(overrides) {
  const trackedKeys = [
    "OPENCLAW_ADMIN_TOKEN",
    "OPENCLAW_HOSTD_URL",
    "OPENCLAW_HOSTD_MOCK",
    "OPENCLAW_HOSTD_EXEC_TIMEOUT_MS",
  ];
  const previous = new Map();
  for (const key of trackedKeys) {
    previous.set(key, process.env[key]);
    delete process.env[key];
  }
  for (const [key, value] of Object.entries(overrides)) {
    if (value == null) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }
  return () => {
    for (const key of trackedKeys) {
      const value = previous.get(key);
      if (value == null) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  };
}

function loadHostdModule({ env = {}, files = {}, fetchImpl }) {
  const restoreEnv = setEnv(env);
  const source = fs.readFileSync(HOSTD_SOURCE_PATH, "utf-8")
    .replace(
      /import\s+\{\s*Agent,\s*fetch as undiciFetch\s*\}\s+from\s+"undici";/,
      'const { Agent, fetch: undiciFetch } = __stubs.undici;'
    )
    .replace(
      /import\s+\{\s*existsSync,\s*readFileSync\s*\}\s+from\s+"fs";/,
      'const { existsSync, readFileSync } = __stubs.fs;'
    )
    .replace(
      /import\s+\{\s*ACTION_TO_HOSTD\s*\}\s+from\s+"\.\/action_registry\.generated";/,
      'const { ACTION_TO_HOSTD } = __stubs.actionRegistry;'
    );
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
    fileName: HOSTD_SOURCE_PATH,
  });
  const module = { exports: {} };
  const stubs = {
    undici: {
      Agent: class Agent {
        close() {}
      },
      fetch: fetchImpl,
    },
    fs: {
      existsSync(filePath) {
        return Object.prototype.hasOwnProperty.call(files, filePath);
      },
      readFileSync(filePath) {
        if (!Object.prototype.hasOwnProperty.call(files, filePath)) {
          throw new Error(`ENOENT: ${filePath}`);
        }
        return files[filePath];
      },
    },
    actionRegistry: {
      ACTION_TO_HOSTD: {
        soma_run_to_done: "soma_run_to_done",
      },
    },
  };
  const runner = new Function(
    "exports",
    "module",
    "require",
    "__filename",
    "__dirname",
    "__stubs",
    transpiled.outputText
  );
  runner(module.exports, module, require, HOSTD_SOURCE_PATH, path.dirname(HOSTD_SOURCE_PATH), stubs);
  return {
    hostd: module.exports,
    cleanup: restoreEnv,
  };
}

test("executeAction resolves hostd admin token from openclaw_hostd.env and sends auth header", async () => {
  let capturedHeaders = null;
  const { hostd, cleanup } = loadHostdModule({
    env: {
      OPENCLAW_HOSTD_URL: "http://127.0.0.1:8877",
    },
    files: {
      [HOSTD_ENV_FILE]: "OTHER_FLAG=1\nOPENCLAW_ADMIN_TOKEN='env-file-token'\n",
    },
    fetchImpl: async (_url, init) => {
      capturedHeaders = init.headers;
      return {
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            ok: true,
            stdout: "",
            stderr: "",
            exitCode: 0,
            artifact_dir: "artifacts/hostd/mock-run",
          }),
      };
    },
  });

  try {
    const result = await hostd.executeAction("soma_run_to_done");
    assert.equal(result.ok, true);
    assert.equal(capturedHeaders["X-OpenClaw-Admin-Token"], "env-file-token");
  } finally {
    cleanup();
  }
});

test("executeAction fails closed with HOSTD_AUTH_MISSING when no admin token source resolves", async () => {
  let fetchCalls = 0;
  const { hostd, cleanup } = loadHostdModule({
    env: {
      OPENCLAW_HOSTD_URL: "http://127.0.0.1:8877",
    },
    fetchImpl: async () => {
      fetchCalls += 1;
      throw new Error("fetch should not be called");
    },
  });

  try {
    const result = await hostd.executeAction("soma_run_to_done");
    assert.equal(fetchCalls, 0);
    assert.equal(result.ok, false);
    assert.equal(result.error_class, "HOSTD_AUTH_MISSING");
    assert.match(result.error, /HOSTD_AUTH_MISSING/);
    assert.match(result.error, /OPENCLAW_ADMIN_TOKEN/);
    assert.match(result.error, /openclaw_admin_token/);
    assert.match(result.error, /openclaw_hostd\.env/);
  } finally {
    cleanup();
  }
});

test("executeAction classifies hostd 403 as HOSTD_FORBIDDEN without leaking the token", async () => {
  const { hostd, cleanup } = loadHostdModule({
    env: {
      OPENCLAW_HOSTD_URL: "http://127.0.0.1:8877",
      OPENCLAW_ADMIN_TOKEN: "env-admin-token",
    },
    fetchImpl: async () => ({
      ok: false,
      status: 403,
      text: async () => JSON.stringify({ error: "Forbidden" }),
    }),
  });

  try {
    const result = await hostd.executeAction("soma_run_to_done");
    assert.equal(result.ok, false);
    assert.equal(result.error_class, "HOSTD_FORBIDDEN");
    assert.match(result.error, /HOSTD_FORBIDDEN/);
    assert.match(result.error, /X-OpenClaw-Admin-Token/);
    assert.match(result.error, /OPENCLAW_ADMIN_TOKEN/);
    assert.doesNotMatch(result.error, /env-admin-token/);
  } finally {
    cleanup();
  }
});
