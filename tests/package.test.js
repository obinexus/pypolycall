'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const binding = require('..');
const metadata = require('../package.json');

assert.equal(metadata.name, 'pypolycall');
assert.equal(metadata.license, 'MIT');
assert.equal(metadata.publishConfig.access, 'public');

// one version across package.json, pyproject.toml, the Python module and
// polycall-binding.json
const root = path.join(__dirname, '..');
const pyprojectVersion = /^version = "([^"]+)"/m.exec(
  fs.readFileSync(path.join(root, 'pyproject.toml'), 'utf8'))[1];
const moduleVersion = /^__version__ = "([^"]+)"/m.exec(
  fs.readFileSync(path.join(root, 'src', 'pypolycall', '__init__.py'), 'utf8'))[1];
assert.equal(metadata.version, pyprojectVersion, 'package.json vs pyproject.toml version');
assert.equal(moduleVersion, pyprojectVersion, '__version__ vs pyproject.toml version');
assert.equal(require('../polycall-binding.json').version, pyprojectVersion,
  'polycall-binding.json vs pyproject.toml version');

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
  require.resolve('pypolycall/examples/pypolycallrc'),
  path.join(__dirname, '..', 'examples', 'pypolycallrc')
);

for (const file of [binding.pyproject, binding.config, binding.manifest]) {
  assert.equal(fs.existsSync(file), true, `missing package file: ${file}`);
}

console.log('pypolycall npm directory index test: PASS');
