import test from 'node:test';
import assert from 'node:assert/strict';

test('queued uploads send the token to the selected collection', async (context) => {
  const previousLocation = globalThis.location;
  const previousXhr = globalThis.XMLHttpRequest;
  const previousDocument = globalThis.document;
  context.after(() => {
    globalThis.location = previousLocation;
    globalThis.XMLHttpRequest = previousXhr;
    globalThis.document = previousDocument;
  });
  globalThis.location = new URL('http://127.0.0.1:8765/biblioteca?collection=references');
  globalThis.document = {getElementById: () => new EventTarget()};

  const request = {headers: {}};
  globalThis.XMLHttpRequest = class {
    upload = {};
    open(method, url) { Object.assign(request, {method, url}); }
    setRequestHeader(name, value) { request.headers[name] = value; }
    send(body) { request.body = body; }
  };

  const {UploadQueue} = await import('../../web/queue.js');
  const queue = {uploads: 0, token: () => 'collection-token', emit() {}};
  const entry = {file: new File(['Test'], 'example.txt'), batchId: 'test-batch', autoIndex: true};
  UploadQueue.prototype.upload.call(queue, entry);

  assert.equal(request.method, 'POST');
  assert.equal(request.url, '/api/admin/upload?collection=references');
  assert.equal(request.headers['X-RAG-Token'], 'collection-token');
  assert.equal(request.body.get('files').name, 'example.txt');
});
