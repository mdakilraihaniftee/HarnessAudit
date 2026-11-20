// Copies Qwen Code stream-json from stdin to a file, adding the receive time to
// every event as "_ha_recv_ts" (epoch seconds). Qwen Code's stream-json carries
// no per-event timestamps; HarnessAudit needs them to order actions across agents.
// Each line is flushed immediately so drain/collection sees progress.
'use strict';
const fs = require('fs');
const readline = require('readline');
const out = process.argv[2];
if (!out) { process.stderr.write('usage: jsonl_stamp.cjs OUTFILE\n'); process.exit(2); }
const fd = fs.openSync(out, 'a');
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on('line', (line) => {
  const ts = (Date.now() / 1000).toFixed(3);
  const trimmed = line.trim();
  let rec;
  if (trimmed.startsWith('{') && trimmed.length > 2) {
    rec = '{"_ha_recv_ts":' + ts + ',' + trimmed.slice(1);
  } else if (trimmed.length) {
    rec = JSON.stringify({ _ha_recv_ts: Number(ts), _ha_raw: line });
  } else {
    return;
  }
  fs.writeSync(fd, rec + '\n');
});
rl.on('close', () => { fs.closeSync(fd); });
