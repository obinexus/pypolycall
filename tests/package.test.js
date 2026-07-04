'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const binding = require('..');
const metadata = require('../package.json');

assert.equal(metadata.name, '@obinexusltd/pypolycall');
assert.equal(metadata.license, 'MIT');
assert.equal(metadata.publishConfig.access, 'public');

const author = typeof metadata.author === 'string'
  ? metadata.author
  : `${metadata.author?.name} <${metadata.author?.email}>`;

assert.equal(
  author,
  'Nnamdi Michael Okpala <okpalan@protonmail.com>',
  'package author must include the expected name and email'
);

const expectedDirectories = {
  src: { metadataKey: 'src', path: 'src' },
  dist: { metadataKey: 'dist', path: 'dist' },
  examples: { metadataKey: 'example', path: 'examples' },
  tests: { metadataKey: 'test', path: 'tests' }
};

for (const [name, expected] of Object.entries(expectedDirectories)) {
  assert.equal(metadata.directories[expected.metadataKey], expected.path);
  assert.equal(fs.statSync(binding.directories[name].root).isDirectory(), true);
  assert.ok(binding.directories[name].files.length > 0, `${name} index is empty`);
}

assert.ok(binding.directories.src.relativeFiles.includes('pypolycall/__init__.py'));
assert.ok(binding.directories.dist.relativeFiles.includes('README.md'));
assert.ok(binding.directories.examples.relativeFiles.includes('pypolycallrc'));
assert.equal(binding.resolve('src', 'pypolycall', '__init__.py'),
  path.join(binding.directories.src.root, 'pypolycall', '__init__.py'));
assert.throws(() => binding.resolve('src', '..', 'package.json'), RangeError);

assert.equal(
  require.resolve('@obinexusltd/pypolycall/examples/pypolycallrc'),
  path.join(__dirname, '..', 'examples', 'pypolycallrc')
);

for (const file of [binding.pyproject, binding.config, binding.manifest]) {
  assert.equal(fs.existsSync(file), true, `missing package file: ${file}`);
}

console.log('pypolycall npm directory index test: PASS');
