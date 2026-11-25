import assert from "node:assert/strict";
import { test } from "node:test";
import { responseError } from "../src/apiErrors.ts";

test("rate rejection explains manual retry for JSON and HTML responses", async () => {
  for (const body of ["<html>Too Many Requests</html>", JSON.stringify({ detail: "Too many requests." })]) {
    const message = await responseError(new Response(body, { status: 429 }), "fallback");
    assert.match(message, /too many requests/i);
    assert.match(message, /try again/i);
  }
});

test("busy response explains retrying without requiring a JSON body", async () => {
  const message = await responseError(new Response("<html>Busy</html>", { status: 503 }), "fallback");
  assert.match(message, /server is busy/i);
  assert.match(message, /try again/i);
});

test("backend and proxy timeouts have a clear message", async () => {
  for (const body of ["<html>Gateway Timeout</html>", JSON.stringify({ detail: "Slicing exceeded the allowed processing time." })]) {
    assert.match(await responseError(new Response(body, { status: 504 }), "fallback"), /took too long/i);
  }
});

test("other failures preserve readable API messages and tolerate invalid JSON", async () => {
  assert.equal(await responseError(Response.json({ detail: "Invalid STL." }, { status: 400 }), "fallback"), "Invalid STL.");
  assert.equal(await responseError(new Response("invalid json", { status: 500 }), "fallback"), "fallback");
  assert.equal(await responseError(Response.json({ detail: [] }, { status: 422 }), "fallback"), "fallback");
});
