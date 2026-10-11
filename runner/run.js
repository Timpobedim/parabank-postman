#!/usr/bin/env node
// Прогон коллекций Postman по списку (feed) через библиотеку newman.
//
// Чем отличается от «запустить newman в цикле»:
//   1. Код возврата честный. Упавшая проверка — это не `err` в колбэке newman, а запись
//      в summary.run.failures. Если смотреть только на `err`, сборка зелёная при любых
//      падениях. Здесь: 0 — всё прошло, 1 — есть неожиданные падения, 2 — прогон не состоялся.
//   2. Известные дефекты — как строгий xfail. Проверка с меткой [PB-014] в названии описывает
//      ожидаемое поведение и падает, пока дефект есть. Такое падение не роняет сборку.
//      Если помеченная проверка прошла, дефект исправлен или проверка сломалась: сборка красная.
//   3. Коллекции идут строго по очереди: ParaBank теряет одновременную запись (PB-013).

"use strict";

const fs = require("fs");
const path = require("path");
const newman = require("newman");

const ROOT = path.resolve(__dirname, "..");
const DEFECT_TAG = /\[(PB-\d{3})\]/;

function parseArgs(argv) {
  const args = { feed: "feeds/full.json", baseUrl: null, reports: "reports", summary: null, quiet: false };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--feed") { args.feed = value; i += 1; }
    else if (key === "--base-url") { args.baseUrl = value; i += 1; }
    else if (key === "--reports") { args.reports = value; i += 1; }
    else if (key === "--summary") { args.summary = value; i += 1; }
    else if (key === "--quiet") { args.quiet = true; }
    else { throw new Error(`неизвестный аргумент: ${key}`); }
  }
  return args;
}

function resolve(file) {
  return path.isAbsolute(file) ? file : path.join(ROOT, file);
}

function runOne(run, args) {
  const reporters = args.quiet ? ["junit", "htmlextra"] : ["cli", "junit", "htmlextra"];
  const envVar = args.baseUrl ? [{ key: "baseUrl", value: args.baseUrl }] : [];
  const options = {
    collection: JSON.parse(fs.readFileSync(resolve(run.collection), "utf8")),
    environment: run.environment ? JSON.parse(fs.readFileSync(resolve(run.environment), "utf8")) : undefined,
    iterationData: run.iterationData ? resolve(run.iterationData) : undefined,
    folder: run.folder,
    envVar,
    timeoutRequest: 10000,
    reporters,
    reporter: {
      junit: { export: path.join(resolve(args.reports), `${run.name}.xml`) },
      htmlextra: { export: path.join(resolve(args.reports), `${run.name}.html`), title: run.name },
    },
  };
  return new Promise((done) => {
    newman.run(options, (err, summary) => done({ err, summary }));
  });
}

// Каждая проверка (assertion) с итогом: прошла или нет, и метка дефекта, если есть.
function assertions(summary) {
  const out = [];
  for (const execution of summary.run.executions) {
    for (const a of execution.assertions || []) {
      const tag = DEFECT_TAG.exec(a.assertion);
      out.push({
        request: execution.item.name,
        iteration: execution.cursor ? execution.cursor.iteration : 0,
        name: a.assertion,
        passed: !a.error,
        message: a.error ? a.error.message : null,
        defect: tag ? tag[1] : null,
      });
    }
  }
  return out;
}

function classify(run, summary) {
  const all = assertions(summary);
  const unexpected = all.filter((a) => !a.passed && !a.defect);
  const expected = all.filter((a) => !a.passed && a.defect);
  const xpass = all.filter((a) => a.passed && a.defect);
  // Ошибки вне проверок: упал скрипт, не ответил сервер, не разобрался JSON.
  const errors = summary.run.failures
    .filter((f) => f.error && f.error.name !== "AssertionError")
    .map((f) => ({ request: f.source && f.source.name, name: f.error.name, message: f.error.message }));
  return { name: run.name, total: all.length, unexpected, expected, xpass, errors };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const feed = JSON.parse(fs.readFileSync(resolve(args.feed), "utf8"));
  const results = [];
  for (const run of feed.runs) {
    const { err, summary } = await runOne(run, args);
    if (err) {
      console.error(`\n${run.name}: прогон не состоялся — ${err.message}`);
      process.exitCode = 2;
      return;
    }
    results.push(classify(run, summary));
  }

  let bad = 0;
  console.log("\nИтог:");
  for (const r of results) {
    const defects = [...new Set(r.expected.map((a) => a.defect))].sort();
    console.log(
      `  ${r.name}: проверок ${r.total}, неожиданных падений ${r.unexpected.length}, ` +
        `ошибок ${r.errors.length}, известных дефектов ${defects.length}` +
        (defects.length ? ` (${defects.join(", ")})` : "") +
        (r.xpass.length ? `, ИСПРАВЛЕНО? ${r.xpass.length}` : ""),
    );
    for (const a of r.unexpected) console.log(`    ✗ ${a.request} → ${a.name}: ${a.message}`);
    for (const e of r.errors) console.log(`    ✗ ${e.request} → ${e.name}: ${e.message}`);
    for (const a of r.xpass) console.log(`    ! ${a.request} → ${a.name}: прошла, хотя помечена ${a.defect}`);
    bad += r.unexpected.length + r.errors.length + r.xpass.length;
  }
  if (args.summary) {
    fs.writeFileSync(resolve(args.summary), JSON.stringify(results, null, 2));
  }
  process.exitCode = bad ? 1 : 0;
}

main().catch((error) => {
  console.error(error.message);
  process.exitCode = 2;
});
