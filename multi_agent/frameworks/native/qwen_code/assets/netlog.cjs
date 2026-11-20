// Logs the real remote endpoint of every TCP connection and every DNS lookup.
const fs = require('fs'); const net = require('net'); const dns = require('dns');
const out = process.env.HA_NETLOG;
function log(rec) { if (out) { try { fs.appendFileSync(out, JSON.stringify({pid: process.pid, ...rec}) + '\n'); } catch (e) {} } }
const origConnect = net.Socket.prototype.connect;
net.Socket.prototype.connect = function (...args) {
  this.once('connect', () => log({kind: 'tcp', remote: this.remoteAddress, port: this.remotePort}));
  this.once('error', (e) => log({kind: 'tcp_error', err: String(e && e.code)}));
  return origConnect.apply(this, args);
};
const origLookup = dns.lookup;
dns.lookup = function (host, ...rest) { log({kind: 'dns', host}); return origLookup.call(this, host, ...rest); };
