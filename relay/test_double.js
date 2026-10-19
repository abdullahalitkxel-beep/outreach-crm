// Local stand-in for Google Apps Script, used only by the automated tests.
// Runs relay/Code.gs unchanged with in-memory versions of the Google services it uses,
// and serves doGet/doPost over HTTP. Behaves like a real deployment: POST /exec answers
// with a 302 redirect, and the JSON is fetched from the redirect target with GET.
//   node relay/test_double.js <port> <admin_key_hash>
const http = require('http');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const port = Number(process.argv[2] || 8792);
const adminHash = process.argv[3] || '';

const signed = (buf) => Array.from(buf, (b) => (b > 127 ? b - 256 : b));
const toBuf = (v) => (typeof v === 'string' ? Buffer.from(v, 'utf8') : Buffer.from(v.map((b) => (b + 256) % 256)));

const Utilities = {
  DigestAlgorithm: { SHA_256: 'sha256' },
  Charset: { UTF_8: 'utf8' },
  computeDigest: (alg, s) => signed(crypto.createHash(alg).update(toBuf(s)).digest()),
  computeHmacSha256Signature: (value, key) => signed(crypto.createHmac('sha256', toBuf(key)).update(toBuf(value)).digest()),
  newBlob: (s) => ({ getBytes: () => signed(Buffer.from(String(s), 'utf8')) }),
  getUuid: () => crypto.randomUUID(),
};

const props = {};
const PropertiesService = {
  getScriptProperties: () => ({
    getProperty: (k) => (k in props ? props[k] : null),
    setProperty: (k, v) => { props[k] = String(v); },
    getProperties: () => ({ ...props }),
  }),
};

const cache = {};
const CacheService = {
  getScriptCache: () => ({
    get: (k) => (cache[k] && cache[k].until > Date.now() ? cache[k].v : null),
    put: (k, v, ttl) => { if (String(v).length > 100000) throw new Error('cache value too large'); cache[k] = { v: String(v), until: Date.now() + (ttl || 600) * 1000 }; },
    remove: (k) => { delete cache[k]; },
  }),
};

const LockService = { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) };

let nextId = 1;
const items = {};                        // id -> {kind, name, parent, content, trashed, created}
const iter = (list) => { let i = 0; return { hasNext: () => i < list.length, next: () => list[i++] }; };
function fileObj(id) {
  const it = items[id];
  return {
    getId: () => id, getName: () => it.name, getDateCreated: () => new Date(it.created),
    getBlob: () => ({ getDataAsString: () => it.content }),
    setContent: (c) => { it.content = String(c); }, setTrashed: (t) => { it.trashed = !!t; },
  };
}
function folderObj(id) {
  const kids = (kind) => Object.keys(items).filter((k) => items[k].parent === id && items[k].kind === kind && !items[k].trashed);
  return {
    getId: () => id,
    getFoldersByName: (n) => iter(kids('folder').filter((k) => items[k].name === n).map(folderObj)),
    getFilesByName: (n) => iter(kids('file').filter((k) => items[k].name === n).map(fileObj)),
    getFiles: () => iter(kids('file').map(fileObj)),
    createFolder: (n) => { const k = 'd' + nextId++; items[k] = { kind: 'folder', name: n, parent: id, created: Date.now() }; return folderObj(k); },
    createFile: (n, c) => { const k = 'f' + nextId++; items[k] = { kind: 'file', name: n, parent: id, content: String(c), created: Date.now() }; return fileObj(k); },
  };
}
const DriveApp = {
  createFolder: (n) => folderObj('root').createFolder(n),
  getFolderById: (id) => { if (!items[id] || items[id].trashed) throw new Error('not found'); return folderObj(id); },
  getFileById: (id) => { if (!items[id]) throw new Error('not found'); return fileObj(id); },
};

const ContentService = {
  MimeType: { JSON: 'application/json' },
  createTextOutput: (s) => ({ content: s, setMimeType() { return this; } }),
};

const code = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8').replace('__ADMIN_KEY_HASH__', adminHash);
const ctx = vm.createContext({ Utilities, PropertiesService, CacheService, LockService, DriveApp, ContentService, JSON, Date, Math, Number, String, Error, parseInt });
vm.runInContext(code, ctx, { filename: 'Code.gs' });

const pending = {};
http.createServer((req, res) => {
  if (req.method === 'POST' && req.url.startsWith('/exec')) {
    let body = '';
    req.on('data', (d) => { body += d; });
    req.on('end', () => {
      let out;
      try { out = ctx.doPost({ postData: { contents: body } }).content; } catch (e) { out = JSON.stringify({ error: String(e), code: 500 }); }
      const k = crypto.randomUUID();
      pending[k] = out;
      res.writeHead(302, { Location: `http://127.0.0.1:${port}/echo?k=${k}` });
      res.end();
    });
  } else if (req.method === 'GET' && req.url.startsWith('/echo')) {
    const k = new URL(req.url, 'http://x').searchParams.get('k');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(pending[k] || '{}');
    delete pending[k];
  } else if (req.method === 'GET' && req.url.startsWith('/exec')) {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(ctx.doGet().content);
  } else if (req.url === '/_files') {      // test helper: what is in the Drive folder
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(Object.values(items).filter((i) => !i.trashed).map((i) => ({ kind: i.kind, name: i.name }))));
  } else { res.writeHead(404); res.end(); }
}).listen(port, '127.0.0.1', () => console.log('relay test double on', port));
