#!/usr/bin/env node
"use strict";

const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const { URL } = require("url");

const listenHost = process.env.BULLPEN_PROXY_HOST || "0.0.0.0";
const listenPort = Number(process.env.BULLPEN_PORT || "5000");
const upstreamHost = process.env.BULLPEN_INTERNAL_HOST || "127.0.0.1";
const upstreamPort = Number(process.env.BULLPEN_INTERNAL_PORT || "15000");
const staticRoot = process.env.BULLPEN_STATIC_ROOT || "/app/static";

const publicStatic = new Set(["/login.html", "/style.css", "/favicon.ico"]);
const staticCache = new Map();
const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".svg", "image/svg+xml"],
  [".ico", "image/x-icon"],
  [".wav", "audio/wav"],
  [".mp3", "audio/mpeg"],
  [".ogg", "audio/ogg"],
]);

function loadStaticCache(root, dir = root) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      loadStaticCache(root, fullPath);
      continue;
    }
    if (!entry.isFile()) {
      continue;
    }
    const stat = fs.statSync(fullPath);
    const urlPath = "/" + path.relative(root, fullPath).split(path.sep).join("/");
    staticCache.set(urlPath, {
      body: fs.readFileSync(fullPath),
      contentType: contentTypes.get(path.extname(fullPath).toLowerCase()) || "application/octet-stream",
      etag: etagFor(stat),
      lastModified: stat.mtime.toUTCString(),
    });
  }
}

function hasSessionCookie(req) {
  return /(?:^|;\s*)session=/.test(req.headers.cookie || "");
}

function staticPathFor(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch {
    return null;
  }
  if (decoded === "/" || decoded.includes("\0")) {
    return null;
  }
  const resolved = path.resolve(staticRoot, "." + decoded);
  const root = path.resolve(staticRoot);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    return null;
  }
  return resolved;
}

function etagFor(stat) {
  return `"${stat.size.toString(16)}-${Math.floor(stat.mtimeMs).toString(16)}"`;
}

function sendStatic(req, res, entry) {
  const headers = {
    "Cache-Control": "no-cache",
    "Connection": "close",
    "ETag": entry.etag,
    "Last-Modified": entry.lastModified,
    "Content-Type": entry.contentType,
  };
  if (req.headers["if-none-match"] === entry.etag || req.headers["if-modified-since"] === entry.lastModified) {
    res.writeHead(304, headers);
    res.end();
    return;
  }
  headers["Content-Length"] = entry.body.length;
  res.writeHead(200, headers);
  if (req.method === "HEAD") {
    res.end();
    return;
  }
  res.end(entry.body);
}

function proxyHttp(req, res) {
  const headers = { ...req.headers, host: `${upstreamHost}:${upstreamPort}`, connection: "close" };
  delete headers["proxy-connection"];
  delete headers["upgrade"];
  const upstream = http.request(
    {
      host: upstreamHost,
      port: upstreamPort,
      method: req.method,
      path: req.url,
      headers,
    },
    (upstreamRes) => {
      const responseHeaders = { ...upstreamRes.headers };
      delete responseHeaders.connection;
      delete responseHeaders["keep-alive"];
      delete responseHeaders["proxy-authenticate"];
      delete responseHeaders["proxy-authorization"];
      delete responseHeaders.te;
      delete responseHeaders.trailer;
      delete responseHeaders.upgrade;
      res.writeHead(upstreamRes.statusCode || 502, responseHeaders);
      upstreamRes.on("data", (chunk) => {
        res.write(chunk);
      });
      upstreamRes.on("end", () => {
        res.end();
      });
    },
  );
  upstream.on("error", (error) => {
    res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(`Bullpen upstream unavailable: ${error.message}\n`);
  });
  req.on("data", (chunk) => {
    upstream.write(chunk);
  });
  req.on("end", () => {
    upstream.end();
  });
}

const server = http.createServer((req, res) => {
  let urlPath = "/";
  try {
    urlPath = new URL(req.url, "http://bullpen.local").pathname;
  } catch {
    proxyHttp(req, res);
    return;
  }
  if ((req.method === "GET" || req.method === "HEAD") && (publicStatic.has(urlPath) || hasSessionCookie(req))) {
    const filePath = staticPathFor(urlPath);
    if (filePath) {
      const cacheKey = "/" + path.relative(path.resolve(staticRoot), filePath).split(path.sep).join("/");
      const entry = staticCache.get(cacheKey);
      if (entry) {
        sendStatic(req, res, entry);
        return;
      }
      proxyHttp(req, res);
      return;
    }
  }
  proxyHttp(req, res);
});

server.on("upgrade", (req, socket, head) => {
  const upstream = net.connect(upstreamPort, upstreamHost, () => {
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n`);
    for (const [name, value] of Object.entries(req.headers)) {
      if (Array.isArray(value)) {
        for (const item of value) {
          upstream.write(`${name}: ${item}\r\n`);
        }
      } else if (value !== undefined) {
        upstream.write(`${name}: ${value}\r\n`);
      }
    }
    upstream.write("\r\n");
    if (head && head.length) {
      upstream.write(head);
    }
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on("error", () => socket.destroy());
});

loadStaticCache(path.resolve(staticRoot));

server.listen(listenPort, listenHost, () => {
  console.error(
    `[bullpen-proxy] listening on ${listenHost}:${listenPort}, upstream ${upstreamHost}:${upstreamPort}, cached ${staticCache.size} static files`,
  );
});
